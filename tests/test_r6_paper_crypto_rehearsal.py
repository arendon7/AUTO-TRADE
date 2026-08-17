from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autotrade.brokers.alpaca_paper_crypto_asset import (
    CRYPTO_ASSET_PATH,
    CRYPTO_PAIR,
    CURRENT_TRADING_API_CRYPTO_EXCHANGE,
    AlpacaPaperCryptoAssetGateway,
    PaperCryptoAssetIntegrityError,
    crypto_asset_path,
    normalize_crypto_pair,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    LATEST_QUOTE_PATH,
    LATEST_TRADE_PATH,
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataGateway,
    AlpacaPaperCryptoMarketDataIntegrityError,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
    AlpacaPaperHttpResponse,
)
from autotrade.brokers.alpaca_paper_market_data import AlpacaPaperMarketDataHttpResponse
from autotrade.domain import MarketSnapshot
import scripts.mac_crypto_paper_rehearsal as rehearsal


NOW = datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)
CREDS = AlpacaPaperCredentials(key_id="paper-key", secret_key="paper-secret")
HASH_A = "a" * 64
HASH_B = "b" * 64


class AssetTransport:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        return AlpacaPaperHttpResponse(
            status_code=200,
            body=json.dumps(self.payload).encode(),
            final_url=request.url,
            headers={"content-type": "application/json", "x-request-id": "req-crypto-asset-1"},
        )


def asset_payload(symbol: str = "BTC/USD", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "276e2673-764b-4ab6-a611-caf665ca6340",
        "class": "crypto",
        "exchange": CURRENT_TRADING_API_CRYPTO_EXCHANGE,
        "symbol": symbol,
        "status": "active",
        "tradable": True,
        "marginable": False,
        "shortable": False,
        "fractionable": True,
        "min_order_size": "0.0001",
        "min_trade_increment": "0.0001",
        "price_increment": "1",
    }
    payload.update(overrides)
    return payload


def test_crypto_pair_normalization_and_asset_path_are_injection_safe() -> None:
    assert normalize_crypto_pair(" eth/usd ") == "ETH/USD"
    assert crypto_asset_path("BTC/USD") == "/v2/assets/BTC%2FUSD"
    assert CRYPTO_ASSET_PATH == "/v2/assets/BTC%2FUSD"
    for value in ("BTCUSD", "BTC/USD?x=1", "../BTC/USD", "BTC//USD", "BTC/BTC", "B/USD"):
        with pytest.raises((TypeError, ValueError)):
            normalize_crypto_pair(value)


def test_crypto_asset_is_exact_get_only_and_preserves_broker_constraints() -> None:
    transport = AssetTransport(asset_payload())
    gateway = AlpacaPaperCryptoAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=transport
    )
    result = gateway.attest_asset(
        credentials=CREDS,
        account_attestation_fingerprint=HASH_A,
        expected_credential_reference=CREDS.credential_reference,
        now=NOW,
    )
    assert result.symbol == CRYPTO_PAIR
    assert result.asset_class == "crypto"
    assert result.exchange == "CRYPTO"
    assert result.min_order_size == Decimal("0.0001")
    assert result.min_trade_increment == Decimal("0.0001")
    assert result.price_increment == Decimal("1")
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == f"https://{ALPACA_PAPER_TRADING_HOST}{CRYPTO_ASSET_PATH}"


def test_crypto_asset_supports_generic_pair_and_url_encoded_slash() -> None:
    transport = AssetTransport(asset_payload("ETH/USD", min_order_size="0.001", price_increment="0.01"))
    result = AlpacaPaperCryptoAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=transport
    ).attest_asset(
        credentials=CREDS,
        account_attestation_fingerprint=HASH_A,
        expected_credential_reference=CREDS.credential_reference,
        now=NOW,
        symbol="eth/usd",
    )
    assert result.symbol == "ETH/USD"
    assert result.source_path == "/v2/assets/ETH%2FUSD"
    assert transport.requests[0].url.endswith("/v2/assets/ETH%2FUSD")


def test_crypto_asset_rejects_requested_response_pair_mismatch_and_stale_exchange_enum() -> None:
    mismatch = AlpacaPaperCryptoAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=AssetTransport(asset_payload("BTC/USD"))
    )
    with pytest.raises(PaperCryptoAssetIntegrityError, match="does not match"):
        mismatch.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint=HASH_A,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
            symbol="ETH/USD",
        )
    stale = AlpacaPaperCryptoAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=AssetTransport(asset_payload(exchange="ALPACA")),
    )
    with pytest.raises(ValueError, match="CRYPTO enum"):
        stale.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint=HASH_A,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("class", "us_equity"),
        ("exchange", "FTX"),
        ("marginable", True),
        ("shortable", True),
        ("tradable", False),
        ("min_order_size", None),
        ("price_increment", "0"),
    ],
)
def test_crypto_asset_fails_closed_on_wrong_product_or_constraints(field: str, value: object) -> None:
    gateway = AlpacaPaperCryptoAssetGateway(
        config=AlpacaPaperGatewayConfig(enabled=True), transport=AssetTransport(asset_payload(**{field: value}))
    )
    with pytest.raises((PaperCryptoAssetIntegrityError, ValueError)):
        gateway.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint=HASH_A,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )


class CryptoMarketTransport:
    def __init__(
        self,
        *,
        symbol: str = "BTC/USD",
        bid: object = "99999",
        ask: object = "100001",
        last: object = "100000",
        quote_observed_at: datetime | None = None,
        trade_observed_at: datetime | None = None,
    ) -> None:
        self.symbol = symbol
        self.bid = bid
        self.ask = ask
        self.last = last
        self.quote_observed_at = quote_observed_at or (NOW - timedelta(seconds=1))
        self.trade_observed_at = trade_observed_at or (NOW - timedelta(seconds=1))
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        if LATEST_QUOTE_PATH in request.url:
            timestamp = self.quote_observed_at.isoformat().replace("+00:00", "Z")
            payload = {
                "quotes": {
                    self.symbol: {
                        "bp": self.bid,
                        "bs": "0.4",
                        "ap": self.ask,
                        "as": "0.5",
                        "t": timestamp,
                    }
                }
            }
        elif LATEST_TRADE_PATH in request.url:
            timestamp = self.trade_observed_at.isoformat().replace("+00:00", "Z")
            payload = {"trades": {self.symbol: {"p": self.last, "s": "0.001", "t": timestamp}}}
        else:
            raise AssertionError(request.url)
        return AlpacaPaperMarketDataHttpResponse(
            status_code=200,
            body=json.dumps(payload).encode(),
            final_url=request.url,
            headers={},
        )


def test_crypto_market_uses_exact_latest_quote_and_trade_gets_and_builds_fresh_snapshot() -> None:
    transport = CryptoMarketTransport()
    gateway = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=transport
    )
    result = gateway.attest_snapshot(credentials=CREDS, now=NOW)
    assert result.market.symbol == "BTC/USD"
    assert result.market.bid == Decimal("99999")
    assert result.market.ask == Decimal("100001")
    assert result.market.last == Decimal("100000")
    assert result.market.observed_at == NOW
    assert result.quote_observed_at == NOW - timedelta(seconds=1)
    assert result.quote_age_seconds == Decimal("1.0")
    assert result.trade_age_seconds == Decimal("1.0")
    assert result.activity_witness == "QUOTE"
    assert len(result.quote_response_sha256) == 64
    assert len(transport.requests) == 2
    assert all(request.method == "GET" for request in transport.requests)
    assert LATEST_QUOTE_PATH in transport.requests[0].url
    assert LATEST_TRADE_PATH in transport.requests[1].url
    assert all("symbols=BTC/USD" in request.url for request in transport.requests)


def test_crypto_market_contract_does_not_use_orderbook_as_quote_freshness_proxy() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src/autotrade/brokers/alpaca_paper_crypto_market_data.py"
    ).read_text(encoding="utf-8")
    assert 'LATEST_QUOTE_PATH = "/v1beta3/crypto/us/latest/quotes"' in source
    assert 'LATEST_TRADE_PATH = "/v1beta3/crypto/us/latest/trades"' in source
    assert "/latest/orderbooks" not in source
    assert 'root.get("quotes")' in source
    assert 'quote.get("bp")' in source
    assert 'quote.get("ap")' in source
    assert 'quote.get("t")' in source
    assert "fresh_activity_age_seconds" in source
    assert "max_reference_age_seconds" in source
    assert "crypto latest quote is stale for execution" in source
    assert "if trade_age <= max_reference:" in source
    assert "crypto latest trade deviates from quote midpoint" in source
    assert "crypto latest trade reference is too old" not in source


def test_crypto_market_supports_second_pair_without_cross_pair_data() -> None:
    transport = CryptoMarketTransport(symbol="ETH/USD", bid="4999", ask="5001", last="5000")
    result = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=transport
    ).attest_snapshot(credentials=CREDS, now=NOW, symbol="eth/usd")
    assert result.market.symbol == "ETH/USD"
    assert result.market.last == Decimal("5000")
    assert all("symbols=ETH/USD" in request.url for request in transport.requests)

    wrong = CryptoMarketTransport(symbol="BTC/USD")
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="ETH/USD"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=wrong
        ).attest_snapshot(credentials=CREDS, now=NOW, symbol="ETH/USD")


def test_crypto_market_requires_fresh_quote_even_when_trade_is_recent() -> None:
    transport = CryptoMarketTransport(
        quote_observed_at=NOW - timedelta(seconds=90),
        trade_observed_at=NOW - timedelta(seconds=2),
    )
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="quote is stale for execution"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=transport
        ).attest_snapshot(credentials=CREDS, now=NOW)


def test_crypto_market_accepts_actual_mac_case_fresh_quote_with_371s_old_trade() -> None:
    transport = CryptoMarketTransport(
        quote_observed_at=NOW - timedelta(seconds=2),
        trade_observed_at=NOW - timedelta(seconds=371, microseconds=705000),
    )
    result = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=transport
    ).attest_snapshot(credentials=CREDS, now=NOW)
    assert result.activity_witness == "QUOTE"
    assert result.quote_age_seconds == Decimal("2.0")
    assert result.trade_age_seconds == Decimal("371.705")
    assert result.market.observed_at == NOW


def test_crypto_market_old_trade_cannot_poison_fresh_quote_but_recent_bad_trade_still_blocks() -> None:
    stale_bad_trade = CryptoMarketTransport(
        bid="99999",
        ask="100001",
        last="50000",
        quote_observed_at=NOW - timedelta(seconds=2),
        trade_observed_at=NOW - timedelta(seconds=371, microseconds=705000),
    )
    result = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=stale_bad_trade
    ).attest_snapshot(credentials=CREDS, now=NOW)
    assert result.market.last == Decimal("50000")
    assert result.quote_age_seconds == Decimal("2.0")
    assert result.trade_age_seconds == Decimal("371.705")

    recent_bad_trade = CryptoMarketTransport(
        bid="99999",
        ask="100001",
        last="50000",
        quote_observed_at=NOW - timedelta(seconds=2),
        trade_observed_at=NOW - timedelta(seconds=2),
    )
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="deviates from quote midpoint"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=recent_bad_trade
        ).attest_snapshot(credentials=CREDS, now=NOW)


def test_crypto_market_rejects_zero_stale_quote_or_unbounded_quote_reference() -> None:
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="positive"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=CryptoMarketTransport(ask="0")
        ).attest_snapshot(credentials=CREDS, now=NOW)

    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="quote is stale for execution"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True),
            transport=CryptoMarketTransport(
                quote_observed_at=NOW - timedelta(seconds=61),
                trade_observed_at=NOW - timedelta(seconds=1),
            ),
        ).attest_snapshot(credentials=CREDS, now=NOW)

    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="reference is too old"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True),
            transport=CryptoMarketTransport(
                quote_observed_at=NOW - timedelta(seconds=301),
                trade_observed_at=NOW - timedelta(seconds=1),
            ),
        ).attest_snapshot(credentials=CREDS, now=NOW)


def test_crypto_rehearsal_runs_profile_safety_and_oms_without_broker_write(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "account_attestation.json").write_text(
        json.dumps(
            {
                "environment": "PAPER",
                "credentials_persisted": False,
                "account_id": "12345678-abcd-abcd-abcd-123456789012",
            }
        ),
        encoding="utf-8",
    )
    account = SimpleNamespace(
        account_id="12345678-abcd-abcd-abcd-123456789012",
        portfolio_value=Decimal("100000"),
        buying_power=Decimal("100000"),
        fingerprint=HASH_A,
        credential_reference=CREDS.credential_reference,
    )
    asset = SimpleNamespace(
        symbol="ETH/USD",
        asset_class="crypto",
        exchange="CRYPTO",
        fractionable=True,
        marginable=False,
        shortable=False,
        min_order_size=Decimal("0.001"),
        min_trade_increment=Decimal("0.001"),
        price_increment=Decimal("0.01"),
        fingerprint=HASH_B,
        response_sha256=HASH_B,
        observed_at=NOW,
    )
    flat = SimpleNamespace(clean_for_first_canary=True, position_count=0, open_order_count=0)
    market_attestation = SimpleNamespace(
        market=MarketSnapshot(
            symbol="ETH/USD",
            bid=Decimal("4999"),
            ask=Decimal("5001"),
            last=Decimal("5000"),
            observed_at=NOW,
        ),
        fingerprint="c" * 64,
        location="us",
        quote_age_seconds=Decimal("2"),
        trade_age_seconds=Decimal("90"),
        activity_witness="QUOTE",
    )

    class AccountGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_account(self, **kwargs): return account

    class AssetGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_asset(self, **kwargs):
            assert kwargs["symbol"] == "ETH/USD"
            return asset

    class FlatGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_flatness(self, **kwargs): return flat

    class MarketGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_snapshot(self, **kwargs):
            assert kwargs["symbol"] == "ETH/USD"
            return market_attestation

    monkeypatch.setattr(rehearsal, "AlpacaPaperAccountGateway", AccountGateway)
    monkeypatch.setattr(rehearsal, "AlpacaPaperCryptoAssetGateway", AssetGateway)
    monkeypatch.setattr(rehearsal, "AlpacaPaperFlatAccountGateway", FlatGateway)
    monkeypatch.setattr(rehearsal, "AlpacaPaperCryptoMarketDataGateway", MarketGateway)
    monkeypatch.delenv(rehearsal.WRITE_ENV, raising=False)

    result = rehearsal.run(workspace_path=workspace, credentials=CREDS, now=NOW, symbol="eth/usd")
    assert result["status"] == "CRYPTO_PAPER_REHEARSAL_PASS"
    assert result["symbol"] == "ETH/USD"
    assert result["exchange"] == "CRYPTO"
    assert result["market_hours_model"] == "CONTINUOUS_24_7"
    assert result["product_protection_model"] == "CRYPTO_STOP_LIMIT"
    assert len(result["product_profile_fingerprint"]) == 64
    assert result["capital_safety"] == "APPROVED"
    assert result["oms_status"] == "VALIDATED"
    assert result["broker_reads"] == 6
    assert result["broker_write_performed"] is False
    assert result["external_post_authorized"] is False
    assert result["external_order_submitted"] is False
    assert result["persistent_crypto_candidate_created"] is False
    assert result["crypto_bracket_supported"] is False
    assert result["capital_authority"] == "NONE"
    assert result["live_trading"] == "BLOCKED"


def test_crypto_lab_surfaces_contain_no_execution_path() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/mac_crypto_paper_rehearsal.py").read_text(encoding="utf-8")
    server = (root / "scripts/mac_crypto_dashboard.py").read_text(encoding="utf-8")
    html = (root / "web/mac_crypto_dashboard.html").read_text(encoding="utf-8")
    launcher = (root / "ABRIR_CRYPTO_PAPER.command").read_text(encoding="utf-8")
    combined = "\n".join((script, server, html, launcher))
    for forbidden in (
        "alpaca_paper_writer",
        "r6_execute_paper_canary.py",
        "stage_external_submission",
        "r6_connectivity_bound_final_freshness.py",
        "export R6_EXTERNAL_PAPER_WRITE=ENABLED",
        "localStorage.setItem",
        "sessionStorage.setItem",
    ):
        assert forbidden not in combined
    assert 'os.environ.get(WRITE_ENV) == "ENABLED"' in script
    assert 'os.environ.get(WRITE_ENV) == "ENABLED"' in server
    assert 'export R6_EXTERNAL_PAPER_WRITE=DISABLED' in launcher
    assert 'broker_write_performed": False' in script
    assert 'external_post_authorized": False' in script
    assert "Crypto PAPER Lab may bind only to 127.0.0.1" in server
