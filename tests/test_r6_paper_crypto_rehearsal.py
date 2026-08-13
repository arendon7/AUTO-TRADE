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
    AlpacaPaperCryptoAssetGateway,
    PaperCryptoAssetIntegrityError,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    LATEST_TRADE_PATH,
    ORDERBOOK_PATH,
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


def asset_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "276e2673-764b-4ab6-a611-caf665ca6340",
        "class": "crypto",
        "exchange": "ALPACA",
        "symbol": "BTC/USD",
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
    assert result.exchange == "ALPACA"
    assert result.min_order_size == Decimal("0.0001")
    assert result.min_trade_increment == Decimal("0.0001")
    assert result.price_increment == Decimal("1")
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == f"https://{ALPACA_PAPER_TRADING_HOST}{CRYPTO_ASSET_PATH}"


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
        config=AlpacaPaperGatewayConfig(enabled=True),
        transport=AssetTransport(asset_payload(**{field: value})),
    )
    with pytest.raises((PaperCryptoAssetIntegrityError, ValueError)):
        gateway.attest_asset(
            credentials=CREDS,
            account_attestation_fingerprint=HASH_A,
            expected_credential_reference=CREDS.credential_reference,
            now=NOW,
        )


class CryptoMarketTransport:
    def __init__(self, *, ask: object = "100001", observed_at: datetime | None = None) -> None:
        self.ask = ask
        self.observed_at = observed_at or (NOW - timedelta(seconds=1))
        self.requests = []

    def read(self, request):
        self.requests.append(request)
        timestamp = self.observed_at.isoformat().replace("+00:00", "Z")
        if ORDERBOOK_PATH in request.url:
            payload = {
                "orderbooks": {
                    "BTC/USD": {
                        "a": [{"p": self.ask, "s": "0.5"}],
                        "b": [{"p": "99999", "s": "0.4"}],
                        "t": timestamp,
                    }
                }
            }
        elif LATEST_TRADE_PATH in request.url:
            payload = {"trades": {"BTC/USD": {"p": "100000", "s": "0.001", "t": timestamp}}}
        else:
            raise AssertionError(request.url)
        return AlpacaPaperMarketDataHttpResponse(
            status_code=200,
            body=json.dumps(payload).encode(),
            final_url=request.url,
            headers={},
        )


def test_crypto_market_uses_exact_two_gets_and_builds_fresh_snapshot() -> None:
    transport = CryptoMarketTransport()
    gateway = AlpacaPaperCryptoMarketDataGateway(
        AlpacaPaperCryptoMarketDataConfig(enabled=True), transport=transport
    )
    result = gateway.attest_snapshot(credentials=CREDS, now=NOW)
    assert result.market.symbol == "BTC/USD"
    assert result.market.bid == Decimal("99999")
    assert result.market.ask == Decimal("100001")
    assert result.market.last == Decimal("100000")
    assert len(transport.requests) == 2
    assert all(request.method == "GET" for request in transport.requests)
    assert ORDERBOOK_PATH in transport.requests[0].url
    assert LATEST_TRADE_PATH in transport.requests[1].url
    assert all("symbols=BTC/USD" in request.url for request in transport.requests)


def test_crypto_market_rejects_zero_or_stale_prices() -> None:
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="positive"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True),
            transport=CryptoMarketTransport(ask="0"),
        ).attest_snapshot(credentials=CREDS, now=NOW)
    with pytest.raises(AlpacaPaperCryptoMarketDataIntegrityError, match="stale"):
        AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True),
            transport=CryptoMarketTransport(observed_at=NOW - timedelta(seconds=61)),
        ).attest_snapshot(credentials=CREDS, now=NOW)


def test_crypto_rehearsal_runs_safety_and_oms_without_broker_write(monkeypatch, tmp_path: Path) -> None:
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
        asset_class="crypto",
        exchange="ALPACA",
        min_order_size=Decimal("0.0001"),
        min_trade_increment=Decimal("0.0001"),
        price_increment=Decimal("1"),
        fingerprint=HASH_B,
        response_sha256=HASH_B,
        observed_at=NOW,
    )
    flat = SimpleNamespace(clean_for_first_canary=True, position_count=0, open_order_count=0)
    market_attestation = SimpleNamespace(
        market=MarketSnapshot(
            symbol="BTC/USD",
            bid=Decimal("99999"),
            ask=Decimal("100001"),
            last=Decimal("100000"),
            observed_at=NOW - timedelta(seconds=1),
        ),
        fingerprint="c" * 64,
        location="us",
    )

    class AccountGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_account(self, **kwargs): return account

    class AssetGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_asset(self, **kwargs): return asset

    class FlatGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_flatness(self, **kwargs): return flat

    class MarketGateway:
        def __init__(self, *args, **kwargs): pass
        def attest_snapshot(self, **kwargs): return market_attestation

    monkeypatch.setattr(rehearsal, "AlpacaPaperAccountGateway", AccountGateway)
    monkeypatch.setattr(rehearsal, "AlpacaPaperCryptoAssetGateway", AssetGateway)
    monkeypatch.setattr(rehearsal, "AlpacaPaperFlatAccountGateway", FlatGateway)
    monkeypatch.setattr(rehearsal, "AlpacaPaperCryptoMarketDataGateway", MarketGateway)
    monkeypatch.delenv(rehearsal.WRITE_ENV, raising=False)

    result = rehearsal.run(workspace_path=workspace, credentials=CREDS, now=NOW)
    assert result["status"] == "CRYPTO_PAPER_REHEARSAL_PASS"
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
        "localStorage.",
        "sessionStorage.",
    ):
        assert forbidden not in combined
    assert 'os.environ.get(WRITE_ENV) == "ENABLED"' in script
    assert 'os.environ.get(WRITE_ENV) == "ENABLED"' in server
    assert 'export R6_EXTERNAL_PAPER_WRITE=DISABLED' in launcher
    assert 'broker_write_performed": False' in script
    assert 'external_post_authorized": False' in script
    assert "Crypto PAPER Lab may bind only to 127.0.0.1" in server
